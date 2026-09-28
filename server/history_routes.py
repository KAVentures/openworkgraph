from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from shared.evidence import rich_evidence_row
from shared.history_policy import active_ai_history_access, read_policy, set_ai_history_access, update_retention, update_run_memory
from shared.lifespan import extend_lifespan
from .db import connect
from . import run_memory
from .history_retention import cleanup_expired_history, delete_history_session, initialize_history_retention, list_history
from .main import ROOT
from .privacy_pipeline import redact_for_display
from .secure_app import app


class RetentionChoice(BaseModel):
    human_mode: str
    human_days: int | None = None
    agent_mode: str
    agent_days: int | None = None
    onboarding_complete: bool = True

class HistoryDeleteRequest(BaseModel): history_session_id: str
class RunMemoryChoice(BaseModel):
    enabled: bool
    days: int = 90
class RunMemoryForget(BaseModel):
    execution_id: str | None = None
    all: bool = False
class AIHistoryAccessRequest(BaseModel):
    mode: str
    since: str | None = None
    until: str | None = None
    expires_minutes: int | None = 60


def _history_startup() -> None: initialize_history_retention()
def _history_shutdown() -> None: cleanup_expired_history(startup=True)
extend_lifespan(app, startup=_history_startup, shutdown=_history_shutdown)


@app.get("/v1/history-policy")
def get_history_policy() -> dict[str, Any]:
    value=read_policy()
    return {"onboarding_complete":bool(value.get("onboarding_complete")),"human_retention":value.get("human_retention"),"agent_retention":value.get("agent_retention"),"history_generation":int(value.get("history_generation") or 1),"upgrade_preserved_existing_history":bool(value.get("upgrade_preserved_existing_history")),"run_memory":value.get("run_memory"),"tradeoff":{"ephemeral":"Live context works, but completed sessions are deleted and cannot support later month/week comparisons.","run_memory":"Unless turned off, a content-free summary of each run (steps, outcome, commands, test results, counts, tokens; no titles, paths or content) is kept on this computer for the chosen number of days, even after its session is deleted by retention. Deleting a session or time range yourself deletes its summary too.","saved":"Saved history stays on this computer until its retention period expires or you delete it.","ai_access_separate":True}}

@app.put("/v1/history-policy")
def set_history_policy(request: RetentionChoice) -> dict[str, Any]:
    try:
        value=update_retention(human_mode=request.human_mode,human_days=request.human_days,agent_mode=request.agent_mode,agent_days=request.agent_days,onboarding_complete=request.onboarding_complete)
        cleanup=cleanup_expired_history(startup=False)
    except (TypeError,ValueError) as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    return {"status":"saved","human_retention":value.get("human_retention"),"agent_retention":value.get("agent_retention"),"onboarding_complete":bool(value.get("onboarding_complete")),"cleanup":cleanup}

@app.get("/v1/run-memory")
def get_run_memory(limit:int=200)->dict[str,Any]:
    return run_memory.summary(limit=max(1,min(int(limit),1000)))

@app.put("/v1/run-memory/policy")
def set_run_memory_policy(request:RunMemoryChoice)->dict[str,Any]:
    try: value=update_run_memory(enabled=request.enabled,days=request.days)
    except (TypeError,ValueError) as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    # Turning memory off deletes it; a shorter period deletes what is now expired.
    return {"status":"saved","run_memory":value.get("run_memory"),"pruned":run_memory.prune()}

@app.post("/v1/run-memory/forget")
def forget_run_memory(request:RunMemoryForget)->dict[str,Any]:
    if request.all: return {"status":"deleted","run_memory_deleted":run_memory.forget_all()}
    if not request.execution_id: raise HTTPException(status_code=400,detail="execution_id or all is required")
    deleted=run_memory.forget_execution(request.execution_id)
    if not deleted: raise HTTPException(status_code=404,detail="run not found in run memory")
    return {"status":"deleted","run_memory_deleted":deleted}

@app.get("/v1/history")
def get_history(since:str|None=None,until:str|None=None,limit:int=200)->dict[str,Any]:
    try: return list_history(since=since,until=until,limit=limit)
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc

@app.post("/v1/history/delete-session")
def delete_session(request:HistoryDeleteRequest)->dict[str,Any]:
    try: result=delete_history_session(request.history_session_id)
    except ValueError as exc: raise HTTPException(status_code=404,detail=str(exc)) from exc
    return {"status":"deleted",**result}

@app.get("/v1/history/ai-access")
def get_ai_history_access()->dict[str,Any]:
    return {"access":active_ai_history_access(),"meaning":"Saving history locally does not grant an AI permission to read saved history."}

@app.post("/v1/history/ai-access")
def change_ai_history_access(request:AIHistoryAccessRequest)->dict[str,Any]:
    try: access=set_ai_history_access(mode=request.mode,since=request.since,until=request.until,expires_minutes=request.expires_minutes)
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    return {"status":"updated","access":access}

@app.post("/v1/history/cleanup")
def run_history_cleanup()->dict[str,Any]: return cleanup_expired_history(startup=False)

@app.get("/v1/history/export-json")
def export_history_json(since:str|None=None,until:str|None=None,include_raw:bool=False,redact_names:bool=False)->Response:
    # Validate the date window through the same catalogue parser first.
    try: catalog=list_history(since=since,until=until,limit=1)
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    clauses=[]; params:list[Any]=[]
    if catalog.get("since"): clauses.append("observed_at >= ?"); params.append(catalog["since"])
    if catalog.get("until"): clauses.append("observed_at < ?"); params.append(catalog["until"])
    where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
    with connect() as conn:
        rows=conn.execute("SELECT * FROM events"+where+" ORDER BY observed_at ASC, id ASC LIMIT 100001",tuple(params)).fetchall()
    if len(rows)>100000: raise HTTPException(status_code=413,detail="History export exceeds 100000 events; choose a smaller date range")
    evidence=[rich_evidence_row(dict(row),include_identity=False) for row in rows]
    payload={"export":{"kind":"retained_history","since":catalog.get("since"),"until":catalog.get("until"),"include_raw":bool(include_raw),"redact_names":bool(redact_names),"canonical_rows":len(evidence)},"history":catalog,"evidence":evidence}
    if redact_names:
        from .ai_context import redact_contextually
        payload=redact_contextually(payload)
    elif not include_raw:
        payload=redact_for_display(payload)
    body=(json.dumps(payload,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
    return Response(body,media_type="application/json",headers={"Content-Disposition":'attachment; filename="openworkgraph-history.json"',"Cache-Control":"no-store"})

@app.get("/history-retention.js",include_in_schema=False)
def history_retention_script()->Response:
    path=ROOT/"dashboard"/"history_retention.js"
    return Response(path.read_text(encoding="utf-8"),media_type="application/javascript",headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})

@app.middleware("http")
async def inject_history_retention(request:Request,call_next):
    response=await call_next(request)
    if request.method.upper()!="GET" or request.url.path!="/" or response.status_code!=200:return response
    if "text/html" not in str(response.headers.get("content-type") or ""):return response
    try:
        if hasattr(response,"body_iterator"):
            chunks=[chunk async for chunk in response.body_iterator]; body=b"".join(chunk if isinstance(chunk,bytes) else str(chunk).encode("utf-8") for chunk in chunks)
        else: body=bytes(getattr(response,"body",b""))
        text=body.decode("utf-8")
    except Exception:return response
    marker='<script src="/history-retention.js"></script>'
    if marker not in text:text=text.replace("</body>",marker+"\n</body>")
    headers=dict(response.headers);headers.pop("content-length",None)
    return HTMLResponse(text,status_code=response.status_code,headers=headers)

__all__=["get_history","get_history_policy"]
