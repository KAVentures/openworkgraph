"""Local session-scoped analysis job console routes."""
from pathlib import Path
from fastapi.responses import FileResponse
from . import event_log_jobs as engine
from . import event_log_job_console as console

_SCRIPT=Path(__file__).resolve().parents[1]/"dashboard"/"analysis_job_console.js"


def add_routes(router,owner,respond,json_body):
    from fastapi import HTTPException, Request

    @router.get("/analysis-job-console.js")
    def script():
        return FileResponse(_SCRIPT,media_type="application/javascript")

    @router.get("/v1/event-log-analysis-jobs")
    def inventory(request: Request):
        return respond(console.inventory,engine.jobs,owner(request))

    @router.post("/v1/event-log-analysis-jobs/clear-terminal")
    async def clear_terminal(request: Request):
        key=owner(request)
        payload=await json_body(request,"terminal job cleanup",strict=True,limit=4096)
        if owner(request)!=key:
            raise HTTPException(403,"Local dashboard session changed")
        return respond(console.clear_terminal,engine.jobs,key,payload)
