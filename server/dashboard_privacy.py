from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .analytics import summary
from .main import BROWSER_STATUS, COLLECTOR_STATUS, ROOT, VERSION
from .privacy_pipeline import redact_for_display
from .secure_app import app


DASHBOARD_PRIVACY_JS = r"""
(() => {
  let privacySummaryLoading=false;

  async function privacySafeLoad(){
    if(privacySummaryLoading) return;
    privacySummaryLoading=true;
    try{
      await window.__owgAuthReady;
      const r=await fetch('/v1/dashboard-summary?limit=25000&scope=current',{cache:'no-store'});
      if(!r.ok) throw new Error('summary unavailable');
      const d=await r.json();
      if(typeof window.renderGlobal==='function') window.renderGlobal(d);
      if(typeof window.renderActive==='function') window.renderActive(d);
    }catch(_){
      if(typeof window.toast==='function') window.toast('Could not reach the local observer. Is the launcher still running?');
    }finally{
      privacySummaryLoading=false;
      if(typeof window.scheduleSummaryRefresh==='function') window.scheduleSummaryRefresh();
    }
  }

  // The base dashboard historically read the rich summary endpoint. Replace the
  // global loader before DOMContentLoaded so every automatic refresh uses the
  // operational-normalized dashboard endpoint instead.
  window.load=privacySafeLoad;

  const privacyLine=document.querySelector('.privacy-line');
  if(privacyLine) privacyLine.textContent='Dashboard shows privacy-normalized workflow data · rich local evidence stays on this computer';
  const searchLabel=document.querySelector('label[for="evidenceSearch"]');
  if(searchLabel) searchLabel.textContent='Search surface or action';

  if(document.readyState!=='loading') privacySafeLoad();
})();
"""


def _safe_collector_status() -> dict[str, Any] | None:
    if not COLLECTOR_STATUS:
        return None
    return {
        "connected": True,
        "received_at": COLLECTOR_STATUS.get("received_at"),
    }


def _safe_browser_status() -> dict[str, Any] | None:
    if not BROWSER_STATUS:
        return None
    return {
        "status": BROWSER_STATUS.get("status"),
        "received_at": BROWSER_STATUS.get("received_at"),
        "sensor_version": BROWSER_STATUS.get("sensor_version"),
        "expected_sensor_version": BROWSER_STATUS.get("expected_sensor_version"),
        "version_ok": BROWSER_STATUS.get("version_ok"),
        "excluded": bool(BROWSER_STATUS.get("excluded")),
    }


@app.get("/v1/dashboard-summary")
def dashboard_summary(limit: int = 25000, scope: str = "current") -> dict[str, Any]:
    """Human-facing summary built exclusively from content-minimized evidence.

    Raw evidence and customer-authorized context remain available to explicit
    local AI/export flows, but arbitrary names, subjects, document titles and URL
    paths never need to cross the localhost dashboard boundary.
    """
    if scope not in {"current", "all"}:
        raise HTTPException(status_code=400, detail="scope must be current or all")
    since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    result = summary(limit=limit, since=since, operational=True)
    result["mode"] = os.getenv("WORKFLOW_OBSERVER_MODE", "observe")
    result["scope"] = scope
    result["run_started_at"] = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT")
    result["version"] = VERSION
    result["collector"] = _safe_collector_status()
    result["browser_sensor"] = _safe_browser_status()
    result["expected_browser_sensor_version"] = str(
        (BROWSER_STATUS or {}).get("expected_sensor_version") or ""
    )
    result["dashboard_data_layer"] = "operational_normalized"
    return redact_for_display(result)


@app.get("/dashboard-privacy.js")
def dashboard_privacy_script() -> Response:
    return Response(DASHBOARD_PRIVACY_JS, media_type="application/javascript")


@app.middleware("http")
async def inject_dashboard_privacy(request: Request, call_next):
    response = await call_next(request)
    if request.method.upper() != "GET" or request.url.path != "/" or response.status_code != 200:
        return response
    if "text/html" not in str(response.headers.get("content-type") or ""):
        return response
    try:
        if hasattr(response, "body_iterator"):
            chunks = [chunk async for chunk in response.body_iterator]
            body = b"".join(
                chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")
                for chunk in chunks
            )
        else:
            body = bytes(getattr(response, "body", b""))
        text = body.decode("utf-8")
    except Exception:
        return response

    marker = '<script src="/dashboard-privacy.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(content=text, status_code=response.status_code, headers=headers)


__all__ = ["app", "dashboard_summary"]
