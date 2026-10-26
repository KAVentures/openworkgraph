"""Job metadata/results are local-dashboard-only and bound to the submitting session."""
from fastapi import HTTPException, Path, Request
from fastapi.responses import JSONResponse, Response
from . import event_log_workspace as workspace
from .event_log_jobs import JobError, jobs, session_key

_JOB_ID = '^job_[a-f0-9]{32}$'


def add_routes(router, require_dashboard, json_body):
    base = '/v1/event-logs/{log_id}/analysis-jobs'

    def owner(request):
        require_dashboard(request)
        return session_key(request.headers['authorization'][len('OWG-Session '):].strip())

    def respond(operation, *args):
        try:
            value = operation(*args)
            return (Response(value, media_type='application/json', headers={'Cache-Control': 'no-store'})
                    if isinstance(value, bytes) else JSONResponse(value, headers={'Cache-Control': 'no-store'}))
        except JobError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except workspace.EventLogWorkspaceError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post(base, status_code=202)
    async def submit(request: Request, log_id: str):
        key = owner(request)
        spec = await json_body(request, 'analysis job')
        result = respond(jobs.submit, key, log_id, spec)
        result.status_code = 202
        return result

    @router.get(base + '/{job_id}')
    def status(request: Request, log_id: str, job_id: str = Path(pattern=_JOB_ID)):
        return respond(jobs.status, owner(request), log_id, job_id)

    @router.get(base + '/{job_id}/result')
    def result(request: Request, log_id: str, job_id: str = Path(pattern=_JOB_ID)):
        return respond(jobs.result, owner(request), log_id, job_id)

    @router.post(base + '/{job_id}/cancel')
    def cancel(request: Request, log_id: str, job_id: str = Path(pattern=_JOB_ID)):
        return respond(jobs.cancel, owner(request), log_id, job_id)

    @router.post(base + '/{job_id}/delete')
    def remove(request: Request, log_id: str, job_id: str = Path(pattern=_JOB_ID)):
        return respond(jobs.remove, owner(request), log_id, job_id)
