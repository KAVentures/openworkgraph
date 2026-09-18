from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .local_auth import (
    bearer_matches,
    browser_server_proof,
    consume_pairing_code,
    create_dashboard_session,
    dashboard_bootstrap_matches,
    dashboard_session_valid,
    ensure_browser_secret,
    ensure_mcp_token,
    local_security_note,
    new_pairing_code,
    verify_browser_authorization,
)
from .main import DASHBOARD, app

DASHBOARD_COOKIE = "owg_dashboard_session"
COLLECTOR_ROUTES = {("POST", "/v1/events"), ("POST", "/v1/heartbeat")}
BROWSER_ROUTES = {
    ("GET", "/v1/browser-context"),
    ("POST", "/v1/browser-events"),
    ("POST", "/v1/browser-heartbeat"),
}
PUBLIC_PATHS = {"/health"}
EXTENSION_PREFIXES = ("chrome-extension://", "moz-extension://", "safari-web-extension://")


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


def _dashboard_authenticated(request: Request) -> bool:
    return dashboard_session_valid(request.cookies.get(DASHBOARD_COOKIE))


def _api_authenticated(request: Request) -> bool:
    return bearer_matches(request.headers.get("authorization")) or _dashboard_authenticated(request)


def _bootstrap_script() -> str:
    """Authenticate the launched dashboard without ever embedding the API token."""
    return r"""
<script>
(() => {
  const nativeFetch = window.fetch.bind(window);
  const fragment = new URLSearchParams((location.hash || '').replace(/^#/, ''));
  const bootstrap = fragment.get('bootstrap') || '';
  if (bootstrap) history.replaceState(null, '', location.pathname + location.search);
  window.__owgAuthReady = (async () => {
    if (!bootstrap) return true;
    try {
      const r = await nativeFetch('/v1/dashboard-session', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({bootstrap}),
        cache: 'no-store'
      });
      return r.ok;
    } catch (_) { return false; }
  })();
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : String(input?.url || '');
    if (url.startsWith('/v1/') && url !== '/v1/dashboard-session') await window.__owgAuthReady;
    return nativeFetch(input, init);
  };
})();
</script>
"""


def _connection_override_script() -> str:
    """Keep MCP credentials behind the authenticated dashboard session."""
    return r"""
<script>
(() => {
  async function connectionConfig(){
    await window.__owgAuthReady;
    const r = await fetch('/v1/mcp-connection-config', {cache:'no-store'});
    if(!r.ok) throw new Error('OpenWorkGraph dashboard session is not authenticated');
    return await r.json();
  }
  window.connectCursor = async function(){
    try{
      const c=await connectionConfig();
      const config=b64(JSON.stringify({url:c.endpoint,headers:{Authorization:`Bearer ${c.token}`}}));
      window.location.href=`cursor://anysphere.cursor-deeplink/mcp/install?name=OpenWorkGraph&config=${encodeURIComponent(config)}`;
    }catch(e){openModal('Connection unavailable','Local security',`<p>${esc(e.message||'Could not create the local MCP connection.')}</p><div class="note">Reopen the dashboard from the OpenWorkGraph launcher and try again.</div>`);}
  };
  window.claudeConfig = function(){
    const ua=(navigator.userAgent||'').toLowerCase();
    if(ua.includes('windows')){
      return JSON.stringify({mcpServers:{openworkgraph:{command:'powershell.exe',args:['-NoProfile','-Command','& "$env:LOCALAPPDATA\\OpenWorkGraph\\.venv\\Scripts\\python.exe" -m mcp_server.secure_stdio'],env:{WORKFLOW_OBSERVER_API:'http://127.0.0.1:8787'}}}},null,2);
    }
    return JSON.stringify({mcpServers:{openworkgraph:{command:'/bin/bash',args:['-lc','"$HOME/Library/Application Support/WorkflowObserver/.venv/bin/python" -m mcp_server.secure_stdio'],env:{WORKFLOW_OBSERVER_API:'http://127.0.0.1:8787'}}}},null,2);
  };
  const originalOpenConnect=window.openConnect;
  window.openConnect=async function(kind){
    if(kind==='other'){
      try{
        const c=await connectionConfig();
        const cfg=JSON.stringify({url:c.endpoint,headers:{Authorization:`Bearer ${c.token}`}},null,2);
        openModal('Other MCP client','Authenticated local connection',`<p>OpenWorkGraph's HTTP MCP endpoint is protected by a local bearer capability. Use both the endpoint and header below.</p><h3>Endpoint</h3><div class="codebox">${esc(c.endpoint)}</div><h3>Configuration</h3><div class="codebox">${esc(cfg)}</div><div class="modal-actions"><button onclick='copyText(${JSON.stringify(cfg)},this)'>Copy config</button></div><div class="note">The token is local to this OpenWorkGraph installation. Do not paste it into websites or logs.</div>`);
      }catch(e){openModal('Connection unavailable','Local security',`<p>${esc(e.message||'Could not create connection details.')}</p>`);} return;
    }
    if(kind==='chatgpt'){
      try{
        const c=await connectionConfig();
        openModal('Connect ChatGPT','Secure MCP Tunnel',`<p>ChatGPT cannot directly reach localhost. Keep OpenWorkGraph running and use OpenAI's Secure MCP Tunnel/custom app flow for <code>${esc(c.endpoint)}</code>.</p><p>The local MCP endpoint now requires an <code>Authorization: Bearer …</code> header. Configure that header in the tunnel/connector if prompted.</p><div class="modal-actions"><button onclick="copyText('${c.endpoint}',this)">Copy endpoint</button><button class="secondary" onclick="copyText('Bearer ${c.token}',this)">Copy authorization value</button><a class="btn secondary" target="_blank" rel="noreferrer" href="https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt">Open ChatGPT setup guide</a></div><div class="note">This token only authorizes the local OpenWorkGraph MCP process. Same-user malware remains outside this prototype's security boundary.</div>`);
      }catch(e){openModal('Connection unavailable','Local security',`<p>${esc(e.message||'Could not create connection details.')}</p>`);} return;
    }
    return originalOpenConnect(kind);
  };
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

    # The page shell contains no local capability. The launcher bootstraps an
    # HttpOnly session using a secret carried in the URL fragment, which is never
    # transmitted in the initial GET request.
    if method == "GET" and path == "/":
        return HTMLResponse(_dashboard_html())

    if path in PUBLIC_PATHS:
        return await call_next(request)

    if path in {"/v1/browser-challenge", "/v1/browser-pair"} and method == "OPTIONS":
        return _extension_cors(Response(status_code=204), origin)

    if path == "/v1/dashboard-session" and method == "POST":
        try:
            payload: dict[str, Any] = await request.json()
        except Exception:
            return _json_error("invalid dashboard bootstrap request", 400)
        if not dashboard_bootstrap_matches(str(payload.get("bootstrap") or "")):
            return _json_error("invalid dashboard bootstrap", 401)
        session = create_dashboard_session()
        response = JSONResponse({"status": "ok"})
        response.set_cookie(
            DASHBOARD_COOKIE,
            session,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
            max_age=12 * 60 * 60,
        )
        return response

    if path == "/v1/browser-challenge" and method == "POST":
        try:
            payload = await request.json()
            nonce = str(payload.get("nonce") or "")
        except Exception:
            nonce = ""
        if not nonce or len(nonce) > 200:
            return _extension_cors(_json_error("invalid challenge", 400), origin)
        return _extension_cors(JSONResponse({"proof": browser_server_proof(nonce)}), origin)

    if path == "/v1/browser-pair" and method == "POST":
        if not origin.startswith(EXTENSION_PREFIXES):
            return _json_error("browser extension origin required", 403)
        try:
            payload = await request.json()
            code = str(payload.get("code") or "")
        except Exception:
            code = ""
        if not consume_pairing_code(code):
            return _extension_cors(_json_error("invalid or expired pairing code", 401), origin)
        return _extension_cors(JSONResponse({"secret": ensure_browser_secret()}), origin)

    if path == "/v1/browser-pairing-code" and method == "POST":
        if not _api_authenticated(request):
            return _json_error("authentication required", 401)
        return JSONResponse(new_pairing_code())

    if path == "/v1/mcp-connection-config" and method == "GET":
        if not _api_authenticated(request):
            return _json_error("authentication required", 401)
        return JSONResponse({
            "endpoint": "http://127.0.0.1:8788/mcp",
            "token": ensure_mcp_token(),
            "security_note": local_security_note(),
        })

    if (method, path) in BROWSER_ROUTES:
        body = await request.body()
        if not verify_browser_authorization(
            request.headers.get("authorization"),
            method=method,
            path=path,
            body=body,
        ):
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


# Export this name explicitly for uvicorn server.secure_app:app.
__all__ = ["app"]
